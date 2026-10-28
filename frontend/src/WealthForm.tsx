import { useId, useState } from 'react';
import type { Product, WealthInput } from './wealth-types';
import type { Account } from './api';
import { parseWealthInputAmount } from './wealth';

type Props = {
  products: Product[];
  accounts: Account[];
  busy: boolean;
  locked?: boolean;
  purchaseAllowed?: boolean;
  onPrepare: (input: WealthInput) => void;
};

export default function CreateWealthForm({ products, accounts, busy, locked=false, purchaseAllowed=true, onPrepare }: Props) {
  const [productId, setProductId] = useState('');
  const [accountId, setAccountId] = useState('');
  const [amount, setAmount] = useState('');
  const errorId=useId();
  const amountCents = parseWealthInputAmount(amount);
  const product = products.find((item) => item.id === productId);
  const account = accounts.find((item) => item.id === accountId);
  const invalidAmount = amount !== '' && amountCents === null;
  const disabled = busy || locked || !product || !account || amountCents === null;

  function prepare(action: WealthInput['action']) {
    if (busy || locked || !product || !account || amountCents === null) return;
    if (action === 'purchase' && !purchaseAllowed) return;
    onPrepare({
      product_id: product.id,
      account_id: account.id,
      action,
      amount_cents: amountCents,
    });
  }

  return (
    <form className="wealth-form" onSubmit={(event) => event.preventDefault()}>
      <label className="wealth-field">
        产品
        <select className="wealth-field" value={productId} disabled={busy}
          onChange={(event) => setProductId(event.target.value)}>
          <option value="">请选择</option>
          {products.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>
      <label className="wealth-field">
        关联账户
        <select className="wealth-field" value={accountId} disabled={busy}
          onChange={(event) => setAccountId(event.target.value)}>
          <option value="">请选择</option>
          {accounts.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>
      <label className="wealth-field">
        金额（元）
        <input className="wealth-field" type="text" inputMode="decimal"
          value={amount} disabled={busy} aria-invalid={invalidAmount} aria-describedby={invalidAmount?errorId:undefined}
          onChange={(event) => setAmount(event.target.value)} />
        {invalidAmount && <span id={errorId} className="wealth-error" role="status">请输入准确数字金额，最多两位小数，1分至100万元</span>}
      </label>
      {account&&<p className="wealth-account-status">所选账户可用余额：{new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}).format(account.available_cents/100)} 元；已有申请占用：{new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}).format(account.reserved_cents/100)} 元。</p>}
      <div className="wealth-actions">
      <button className="wealth-preparebutton" type="button" disabled={disabled || !purchaseAllowed}
        onClick={() => prepare('purchase')}>准备模拟申购</button>
      <button className="wealth-preparebutton wealth-secondary" type="button" disabled={disabled}
        onClick={() => prepare('redeem')}>准备模拟赎回</button>
      </div>
    </form>
  );
}
