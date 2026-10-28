export const fid = 'a'.repeat(32)
export const opid = 'op_' + 'b'.repeat(32)
export function flow() {
  const actions = ['birthday_budget','birthday_choice','birthday_catalog','birthday_catalog','birthday_order','birthday_order','birthday_summary']
  return { id:fid,recipient_label:'妈妈',event_date:'2026-10-29',account_id:'a_daily',budget_cents:100000,cake_choice:null,status:'ACTIVE',version:1,simulated:true,merchant_connected:false,
    nodes:actions.map((type,idx)=>({idx,type,...([2,4].includes(idx)?{gift:'flowers'}:[3,5].includes(idx)?{gift:'cake'}:{}),state:'PENDING',observed_state:'PENDING',version:1,operation_id:null})) }
}
export function bundle() { return {flows:[flow()],accounts:[{id:'a_daily',name:'日常账户',masked_number:'**** 2866'}],business_date:'2026-10-28',items:[{item_id:'flowers_simple',name:'简约花束',gift:'flowers',price_cents:30000,quote_version:1,simulated:true,merchant_connected:false},{item_id:'cake_small',name:'小蛋糕',gift:'cake',price_cents:20000,quote_version:1,simulated:true,merchant_connected:false}]} }
export function budgetOp() { return {id:opid,type:'birthday_budget_reserve',status:'WAITING_CONFIRMATION',risk:'YELLOW',created_at:1793160000.123456,expires_at:1793160300.123456,confirmed_at:null,executed_day:null,payload:{account_id:'a_daily',account_name:'日常账户',amount_cents:100000,event_date:'2026-10-29',risk_epoch:'',simulated:true},result:null} }
export function linkedFlow() {const f=flow();f.nodes[0]={...f.nodes[0],state:'WAITING_CONFIRMATION',observed_state:'WAITING_CONFIRMATION',version:3,operation_id:opid};f.version=2;return f}
export function intent() {return {recipient_label:'妈妈',event_date:'2026-10-29',account_id:'a_daily',budget_cents:100000}}
