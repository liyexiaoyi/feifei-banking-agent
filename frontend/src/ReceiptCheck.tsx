import React from 'react';

export type Receipt = {
  operation: { id: string; type: string; status: string; payload?:{account_name?:string;product_name?:string;payee_name?:string;merchant?:string;card_name?:string;masked_number?:string;amount_cents?:number}; result: null | { transaction_id?: string } };
  recorded_status: string;
  observed_status: string;
  read_only: true;
  simulated: true;
  checked_at: string;
};

type Props = {
  receipt: Receipt | null;
  busy: boolean;
  error: string;
  onCheck: () => void;
  onClose: () => void;
};

const STATUS_ZH: Record<string, string> = {
  WAITING_CONFIRMATION: '等待确认',
  WAITING_VERIFICATION: '等待核验',
  SUCCEEDED: '已成功',
  CANCELLED: '已取消',
  EXPIRED: '已过期',
  FAILED: '失败',
  UNKNOWN: '状态未知',
  MANUAL_REVIEW: '需要核查',
  EXECUTING: '执行中',
};

const zh = (s: string) => STATUS_ZH[s] ?? s;

const wrap: React.CSSProperties = { overflowWrap: 'anywhere', minHeight: 48, padding: 24 };

export default function ReceiptCheck({ receipt, busy, error, onCheck, onClose }: Props) {
  const payload=receipt?.operation.payload,kind=receipt?.operation.type;
  const details:[string,string][]=[];
  const add=(label:string,value:unknown)=>{if(typeof value==='string'&&value.length>0)details.push([label,value])};
  if(kind==='transfer'){add('收款人',payload?.payee_name);add('付款账户',payload?.account_name)}
  else if(kind==='wealth_purchase'||kind==='wealth_redemption'){add('演示产品',payload?.product_name);add('关联账户',payload?.account_name)}
  else if(kind==='card_report_lost'||kind==='card_restore'||kind==='virtual_card_create'){add('卡片名称',payload?.card_name);add('关联账户',payload?.account_name);add('卡片掩码',payload?.masked_number)}
  else if(kind==='subscription_query'||kind==='subscription_cancel')add('商户',payload?.merchant);
  const amount=payload?.amount_cents;
  if((kind==='transfer'||kind==='wealth_purchase'||kind==='wealth_redemption')&&typeof amount==='number'&&Number.isSafeInteger(amount)&&amount>0&&amount<=100000000){
    details.push([kind==='transfer'?'转账金额':kind==='wealth_purchase'?'申购金额':'赎回金额',new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}).format(amount/100)+' 元'])
  }

  return (
    <section className="panel receipt-check" style={wrap} aria-label="只读回执核对" aria-live="polite">
      <h3 className="section-title">回执核对</h3>
      <p>本功能为只读模拟：仅读取与展示既有回执，不会发起付款、写入数据或改变任何状态。</p>
      {error ? <p role="alert">错误：{error}</p> : null}
      {receipt && !error ? (
        <dl>
          <dt>操作编号</dt>
          <dd>{receipt.operation.id}</dd>
          <dt>操作类型</dt>
          <dd>{{transfer:'转账',subscription_query:'查询订阅',subscription_cancel:'取消续费',card_report_lost:'挂失卡片',card_restore:'解除挂失',virtual_card_create:'申请虚拟卡',wealth_purchase:'模拟申购',wealth_redemption:'模拟赎回'}[receipt.operation.type]||'业务操作'}</dd>
          {details.map(([label,value])=><React.Fragment key={label}><dt>{label}</dt><dd>{value}</dd></React.Fragment>)}
          <dt>交易标识</dt>
          <dd>{receipt.operation.result?.transaction_id ?? '暂无已记录流水编号'}</dd>
          <dt>记录状态</dt>
          <dd>{zh(receipt.recorded_status)}</dd>
          <dt>当前核对状态</dt>
          <dd>{zh(receipt.observed_status)}</dd>
          <dt>结论</dt>
          <dd>{receipt.observed_status === 'SUCCEEDED' ? '原操作已完成' : '本次只核对既有状态，未重新执行业务'}</dd>
          <dt>核对时间</dt>
          <dd>{new Date(receipt.checked_at).toLocaleString('zh-CN')}</dd>
        </dl>
      ) : (
        !error && <p>尚未核对，请点击“重新核对回执”获取最新回执。</p>
      )}
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="outline-button" style={{ minHeight: 48 }} onClick={onCheck} disabled={busy}>
          {busy ? '核对中…' : '重新核对回执'}
        </button>
        <button className="outline-button" style={{ minHeight: 48 }} onClick={onClose} disabled={busy}>
          关闭
        </button>
      </div>
    </section>
  );
}