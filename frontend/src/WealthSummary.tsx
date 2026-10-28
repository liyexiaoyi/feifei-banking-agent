import type {WealthSnapshot} from './wealth-types'
const money=(n:number)=>new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}).format(n/100)
const units=(n:number)=>{const rest=String(n%10000).padStart(4,'0').replace(/0+$/,'');return String(Math.trunc(n/10000))+(rest?'.'+rest:'')}
const labels:Record<string,string>={WAITING_CONFIRMATION:'等待确认，尚未成交',WAITING_VERIFICATION:'等待模拟验证，尚未成交',READY:'等待办理',UNKNOWN:'结果待核实',EXECUTING:'办理结果待核实',MANUAL_REVIEW:'待本地核对',SUCCEEDED:'模拟已完成'}
export default function WealthSummary({snapshot,busy,onReceipt}:{snapshot:WealthSnapshot;busy:boolean;onReceipt:(id:string)=>void}){
 const pending=snapshot.applications.filter(op=>!['SUCCEEDED','CANCELLED','EXPIRED','FAILED'].includes(op.status))
 const history=snapshot.applications.filter(op=>op.status==='SUCCEEDED').slice(0,5)
 return <div className="wealth-summary"><h3>本人模拟持仓</h3>
  {snapshot.positions.length===0&&<p>当前没有模拟持仓。</p>}
  <div className="wealth-position-list">{snapshot.positions.map(p=><article className="wealth-position" key={p.account_id+':'+p.product_id}><h4>{p.product_name}</h4><p>关联账户：{p.account_name}</p><dl>
   <div><dt>持有份额</dt><dd>{units(p.units_1e4)} 份</dd></div><div><dt>占用份额</dt><dd>{units(p.reserved_units_1e4)} 份</dd></div><div><dt>可用份额</dt><dd>{units(p.available_units_1e4)} 份</dd></div>
   <div><dt>持仓成本</dt><dd>{money(p.cost_cents)} 元</dd></div><div><dt>当前演示估值</dt><dd>{money(p.value_cents)} 元</dd></div><div><dt>账面差额（演示）</dt><dd>{money(p.gain_cents)} 元</dd></div>
  </dl></article>)}</div><p>份额是演示产品的数量。账面差额 = 当前演示估值 − 持仓成本。</p>
  {(pending.length>0||history.length>0)&&<h3>已有模拟理财申请</h3>}
  {[...pending,...history].map(op=><article className="wealth-receipt" key={op.id}><b>{op.payload.product_name} · {op.payload.action==='redeem'?'模拟赎回':'模拟申购'}</b><p>{op.payload.account_name} · {money(op.payload.amount_cents||0)} 元</p><p>{labels[op.status]||'结果待核实'}</p><button type="button" disabled={busy} onClick={()=>onReceipt(op.id)}>核对原申请</button></article>)}
 </div>
}
