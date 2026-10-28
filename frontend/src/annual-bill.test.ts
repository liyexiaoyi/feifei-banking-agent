import test from 'node:test'
import assert from 'node:assert/strict'
import {parseAnnualBill, AnnualBillController} from './annual-bill.ts'

function packet(year='2026'){
 const transactions=[{id:'one',kind:'EXPENSE',amount_cents:1,category:'餐饮',merchant:'商户',occurred_at:year+'-01-01T10:00:00+08:00'}]
 return {year,period:year,simulated:true,expense_cents:1,refund_cents:0,income_cents:0,net_expense_cents:1,previous_net_expense_cents:0,categories:[{name:'餐饮',amount_cents:1}],transactions,evidence_ids:['one'],flags:[],months:Array.from({length:12},(_,i)=>({period:year+'-'+String(i+1).padStart(2,'0'),expense_cents:i===0?1:0,refund_cents:0,income_cents:0,net_expense_cents:i===0?1:0,transaction_count:i===0?1:0}))}
}

test('annual response is exact, current-year bound and whitelisted',()=>{
 const p=packet();const good=parseAnnualBill({...p,user_id:'secret',transactions:[{...p.transactions[0],private:'secret'}]},'2026')
 assert.ok(good);assert.equal(good.months.length,12);assert.doesNotMatch(JSON.stringify(good),/secret|user_id|private/)
 for(const raw of [null,{}, {...p,year:'2025'}, {...p,simulated:false},{...p,expense_cents:1.1},{...p,net_expense_cents:2},{...p,months:p.months.slice(0,11)}, {...p,months:p.months.map((m,i)=>i===1?{...m,period:'2026-01'}:m)},{...p,months:p.months.map((m,i)=>i===0?{...m,expense_cents:2}:m)}, {...p,evidence_ids:['missing']},{...p,flags:[{type:'large_expense',message:'待核查',evidence_ids:['other']}]},{...p,categories:[{name:'餐饮',amount_cents:2}]},{...p,transactions:[{...p.transactions[0],occurred_at:'2025-01-01'}]},{...p,expense_cents:Number.MAX_SAFE_INTEGER+1}]) assert.equal(parseAnnualBill(raw,'2026'),null)
})

test('a valid empty year is distinguishable from unavailable',()=>{
 const p=packet('2024');p.transactions=[];p.evidence_ids=[];p.categories=[];p.expense_cents=p.net_expense_cents=0;p.months=p.months.map(m=>({...m,expense_cents:0,net_expense_cents:0,transaction_count:0}))
 const report=parseAnnualBill(p,'2024');assert.ok(report);assert.equal(report.transactions.length,0)
})

test('late response never overwrites a newer selected year',async()=>{
 const pending=[] as ((v:unknown)=>void)[];const changes=[] as any[]
 const controller=new AnnualBillController(async()=>new Promise(resolve=>pending.push(resolve)),s=>changes.push(s))
 const old=controller.load('2025');const current=controller.load('2026')
 pending[1](packet('2026'));await current;assert.equal(controller.state.report?.year,'2026')
 pending[0](packet('2025'));await old;assert.equal(controller.state.report?.year,'2026');assert.equal(changes.at(-1).year,'2026')
})

test('switching clears old data immediately; errors do not mean a zero bill',async()=>{
 let fail=false;const paths=[] as string[];const controller=new AnnualBillController(async path=>{paths.push(path);if(fail)throw new Error('private diagnostic');return packet('2026')},()=>{})
 await controller.load('2026');assert.ok(controller.state.report)
 fail=true;const work=controller.load('2025');assert.equal(controller.state.report,null);assert.equal(controller.state.loading,true);await work
 assert.equal(controller.state.report,null);assert.ok(controller.state.error);assert.doesNotMatch(controller.state.error,/private/)
 assert.deepEqual(paths,['/bill/annual?year=2026','/bill/annual?year=2025'])
})

test('dispose and invalid selection prevent stale UI and malformed requests',async()=>{
 let resolve!: (value:unknown)=>void;let calls=0,emitted=0
 const controller=new AnnualBillController(async()=>{calls++;return new Promise(r=>resolve=r)},()=>{emitted++})
 await controller.load('２０２６');assert.equal(calls,0);assert.ok(controller.state.error)
 const work=controller.load('2026');const before=emitted;controller.dispose();resolve(packet());await work;assert.equal(emitted,before)
})

test('month totals and evidence must account for every actual transaction exactly once',()=>{
 const p=packet()
 const moved=p.months.map((m,i)=>({...m,expense_cents:i===1?1:0,net_expense_cents:i===1?1:0,transaction_count:i===1?1:0}))
 for(const raw of [{...p,evidence_ids:[]},{...p,evidence_ids:['one','one']},{...p,months:p.months.map(m=>({...m,transaction_count:0}))},{...p,months:moved},{...p,transactions:[{...p.transactions[0],occurred_at:'2026-02-30T10:00:00+08:00'}]}])assert.equal(parseAnnualBill(raw,'2026'),null)
})
