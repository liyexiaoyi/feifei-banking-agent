import type { AnnualBill } from "./annual-bill";
import './annual-bill.css';

type Props = {
  year: string;
  report: AnnualBill | null;
  loading: boolean;
  error: string;
  onYearChange: (year: string) => void;
  onRefresh: () => void;
};

type AnnualTx = AnnualBill["transactions"][number];

const money = new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY" });

const kindText: Record<string, string> = {
  EXPENSE: "支出",
  REFUND: "退款",
  INCOME: "收入",
  INTERNAL: "本人账户划转",
  INVESTMENT_PURCHASE: "模拟申购",
  INVESTMENT_REDEMPTION: "模拟赎回",
};

const flagText: Record<string, string> = {
  large_expense: "大额支出",
  possible_duplicate: "可能重复",
};

const buttonStyle = { minHeight: 48, minWidth: 48, padding: "0 16px", font: "inherit" };
const inputStyle = { minHeight: 48, font: "inherit" };

function formatCents(cents: number): string {
  if (typeof cents !== "number" || !Number.isFinite(cents)) return "金额待核查";
  return money.format(cents / 100);
}

function formatTime(value: string): string {
  if (!value) return "时间待核查";
  return value.replace("T", " ");
}

function isYear(value: string): boolean {
  if (!/^[0-9]{4}$/.test(value)) return false;
  const n = Number(value);
  return n >= 1900 && n <= 2100;
}

function renderEvidence(ids: string[], byId: Map<string, AnnualTx>) {
  if (ids.length === 0) return <p>没有附带证据，待核查。</p>;
  return (
    <ul>
      {ids.map((id, i) => {
        const tx = byId.get(id);
        if (!tx) return <li key={id + "-" + i}>证据 {id} 未找到，待核查。</li>;
        return (
          <li key={id + "-" + i}>
            证据：{tx.merchant || "商户待核查"}，{kindText[tx.kind] ?? tx.kind}{" "}
            {formatCents(tx.amount_cents)}，时间 {formatTime(tx.occurred_at)}
          </li>
        );
      })}
    </ul>
  );
}

function renderReport(report: AnnualBill) {
  const byId = new Map<string, AnnualTx>();
  for (const tx of report.transactions) byId.set(tx.id, tx);
  const noTransactions = report.transactions.length === 0;

  return (
    <div>
      <p>
        <strong>模拟数据</strong>：以下金额仅供演示，不是真实银行流水。
      </p>

      <div className="annual-bill-summary">
        <div><small>全年支出</small><b>{formatCents(report.expense_cents)}</b></div>
        <div><small>全年退款</small><b>{formatCents(report.refund_cents)}</b></div>
        <div><small>全年收入</small><b>{formatCents(report.income_cents)}</b></div>
        <div><small>全年净支出</small><b>{formatCents(report.net_expense_cents)}</b></div>
        <div><small>上一年净支出</small><b>{formatCents(report.previous_net_expense_cents)}</b></div>
      </div>
      <p>统计依据：{report.evidence_ids.length} 条模拟流水，净支出为支出减退款。</p>

      {noTransactions ? <p>{report.year}年没有流水。</p> : null}

      <h3>每月明细</h3>
      {report.months.length !== 12 ? (
        <p>月份数据不完整（{report.months.length}/12），待核查。</p>
      ) : null}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">月份</th>
              <th scope="col">支出</th>
              <th scope="col">退款</th>
              <th scope="col">收入</th>
              <th scope="col">净支出</th>
              <th scope="col">笔数</th>
            </tr>
          </thead>
          <tbody>
            {report.months.map((m, i) => (
              <tr key={m.period + "-" + i}>
                <th scope="row">{m.period}</th>
                <td>{formatCents(m.expense_cents)}</td>
                <td>{formatCents(m.refund_cents)}</td>
                <td>{formatCents(m.income_cents)}</td>
                <td>{formatCents(m.net_expense_cents)}</td>
                <td>{m.transaction_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>待核查（{report.flags.length}项）</h3>
      <p>提示依据金额和时间规则，需核对实际情况，不能据此认定诈骗。</p>
      {report.flags.length === 0 ? (
        <p>暂无待核查项目。</p>
      ) : (
        <ul>
          {report.flags.map((f, i) => (
            <li key={f.type + "-" + i}>
              <p>
                <strong>【待核查·{flagText[f.type] ?? "其他"}】</strong>
                {f.message}
              </p>
              {renderEvidence(f.evidence_ids, byId)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function AnnualBillPanel(props: Props) {
  const { year, report, loading, error, onYearChange, onRefresh } = props;
  const validYear = isYear(year);

  let body;
  if (!validYear) {
    body = <p role="alert">年份无效，请输入1900至2100之间的年份。</p>;
  } else if (loading) {
    body = <p role="status">正在读取{year}年的年度账单，请稍候。</p>;
  } else if (error) {
    body = (
      <div role="alert">
        <p>读取失败：{error}</p>
        <p>旧数据已隐藏，请点击“重试”。</p>
      </div>
    );
  } else if (!report) {
    body = <p>还没有{year}年的账单数据，请点击“刷新”查询。</p>;
  } else if (report.year !== year) {
    body = (
      <p role="alert">
        收到的账单年份（{report.year}）与查询年份（{year}）不一致，已隐藏，请点击“刷新”。
      </p>
    );
  } else {
    body = renderReport(report);
  }

  return (
    <section className="panel details-panel annual-bill-panel" aria-label="年度账单">
      <h2>年度账单（模拟数据）</h2>
      <p>查看全年收支与每月明细，核对需要留意的消费。</p>
      <div className="annual-bill-controls">
        <label>
          查询年份{" "}
          <input
            type="number"
            min={1900}
            max={2100}
            step={1}
            aria-label="查询年份"
            value={year}
            style={inputStyle}
            onChange={(event: { target: { value: string } }) => onYearChange(event.target.value)}
          />
        </label>{" "}
        <button
          className="outline-button"
          type="button"
          style={buttonStyle}
          disabled={loading || !validYear}
          onClick={() => onRefresh()}
        >
          {error ? "重试" : "刷新"}
        </button>
      </div>
      {body}
    </section>
  );
}
