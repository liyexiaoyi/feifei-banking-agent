type Op={id:string,type:string,status:string,risk?:string,payload?:any,result?:any}|null

const DUE=/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00$/
const str=(v:unknown)=>typeof v==='string'&&v!==''

export default function ScheduledSummary(props:{op:Op}){
  const op=props.op
  if(!op)return <div className="sched">正在只读读取授权回执</div>
  const p=op.payload||{}
  const ok=op.type==='scheduled_transfer_authorize'&&Number.isSafeInteger(p.amount_cents)&&p.amount_cents>=1&&p.amount_cents<=100000000&&typeof p.due_at_iso==='string'&&DUE.test(p.due_at_iso)&&str(p.payee_name)&&str(p.payee_phone)&&str(p.account_name)
  if(!ok)return <div className="sched" role="alert">无法显示定时授权详情，请核对原回执</div>
  const c:number=p.amount_cents
  const money=Math.floor(c/100)+'.'+String(c%100).padStart(2,'0')+' 元'
  const recorded=op.status==='SUCCEEDED'&&op.result?.status==='ACTIVE'
  return <div className="sched" aria-label="定时转账授权摘要">
    <p className="sched-status">{recorded?'授权已记录，此回执不是付款结果':'授权详情，不是付款结果'}</p>
    <p>执行时间：{p.due_at_iso.slice(0,10)} {p.due_at_iso.slice(11,16)}（北京时间）</p>
    <p>付款账户：{p.account_name}</p>
    <p>收款人：{p.payee_name}（{p.payee_phone}）</p>
    <p>金额：{money}</p>
    <p className="sched-note">授权不是付款，不预留资金。到期时重新检查，超过24小时不补付。</p>
  </div>
}
