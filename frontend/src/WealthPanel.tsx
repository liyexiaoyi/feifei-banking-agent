import type {Account} from './api'
import type {WealthSnapshot,WealthInput} from './wealth-types'
import WealthForm from './WealthForm'
import WealthSummary from './WealthSummary'
import './wealth.css'
type Props={snapshot:WealthSnapshot|null;accounts:Account[]|null;loading:boolean;busy:boolean;locked?:boolean;purchaseAllowed?:boolean;error:string;onPrepare:(input:WealthInput)=>void;onReceipt:(id:string)=>void;onRefresh:()=>void}
export default function WealthPanel({snapshot,accounts,loading,busy,locked=false,purchaseAllowed=true,error,onPrepare,onReceipt,onRefresh}:Props){
 return <section className="wealth-panel" aria-label="当前模拟理财" aria-busy={loading||busy}>
  <header><h2>模拟理财</h2><button type="button" disabled={loading||busy} onClick={onRefresh}>刷新模拟理财</button></header>
  <p className="wealth-notice">合成产品仅用于本机演示。申购与赎回都需独立确认和模拟验证；固定演示估值不代表真实报价或预期收益。</p>
  {error?<p role="alert">{error}</p>:loading?<p role="status">正在核对当前模拟产品、持仓与申请…</p>:!snapshot||!accounts?<p role="status">暂时无法核对模拟理财，请刷新重试。</p>:<>
   {!purchaseAllowed&&<p role="status">新的模拟申购需先完成并核对当前演示测评。您仍可查看持仓或准备模拟赎回。</p>}
   <WealthForm products={snapshot.products} accounts={accounts} busy={busy} locked={locked} purchaseAllowed={purchaseAllowed} onPrepare={onPrepare}/>
   <WealthSummary snapshot={snapshot} busy={busy} onReceipt={onReceipt}/>
  </>}
 </section>
}
