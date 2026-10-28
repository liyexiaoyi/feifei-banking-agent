import test from 'node:test'
import assert from 'node:assert/strict'
import {parseRecurringInsights,parseReminders,RecurringRemindersController} from './recurring-reminders.ts'

const candidate={merchant:'周期商户',amount_cents:100,occurrences:2,evidence_ids:['t1','t2'],last_seen:'2026-10-27T10:00:00+08:00',rule:'相邻25—35天',verified:false}
const reminder={id:'rem_example',merchant:'提醒商户',amount_cents:100,charge_date:'2026-10-30',acknowledged:false,version:1,simulated:true}
const recurring={simulated:true,as_of:'2026-10-27',candidates:[candidate]}
const reminders={simulated:true,reminders:[reminder]}

test('candidate and reminder DTOs are strict, exact and private-field free',()=>{
 const a=parseRecurringInsights({...recurring,user_id:'secret',candidates:[{...candidate,private:'secret'}]});assert.ok(a);assert.doesNotMatch(JSON.stringify(a),/secret|user_id|private/)
 const b=parseReminders({...reminders,reminders:[{...reminder,subscription_id:'secret'}]});assert.ok(b);assert.doesNotMatch(JSON.stringify(b),/secret|subscription_id/)
 for(const changes of [{amount_cents:1.5},{amount_cents:Number.MAX_SAFE_INTEGER+1},{verified:true},{occurrences:3},{evidence_ids:['t1','t1']}])assert.equal(parseRecurringInsights({...recurring,candidates:[{...candidate,...changes}]}),null)
 for(const changes of [{amount_cents:1.5},{version:true},{acknowledged:'yes'},{simulated:false},{charge_date:'2026-02-30'}])assert.equal(parseReminders({...reminders,reminders:[{...reminder,...changes}]}),null)
})

test('without consent only green bill candidates are read, no reminder check',async()=>{
 const paths=[] as string[];let writes=0
 const c=new RecurringRemindersController(async path=>{paths.push(path);return recurring},async()=>{writes++;return reminders},()=>{})
 await c.load(false);assert.deepEqual(paths,['/bill/recurring']);assert.equal(writes,0);assert.equal(c.state.reminders,null);assert.equal(c.state.candidates?.length,1)
})

test('check is single in-flight and load clears old data immediately',async()=>{
 let release!: (v:unknown)=>void,writes=0
 const c=new RecurringRemindersController(async()=>recurring,async()=>{writes++;return new Promise(r=>release=r)},()=>{})
 const work=c.load(true);await c.load(true);assert.equal(writes,1);assert.equal(c.state.loading,true);release(reminders);await work;assert.equal(c.state.reminders?.length,1)
})

test('consent expiry hides old amounts and stops subsequent automatic checks',async()=>{
 let expired=false,writes=0
 const c=new RecurringRemindersController(async()=>recurring,async()=>{writes++;if(expired)throw {status:403,code:'SUBSCRIPTION_CONSENT_REQUIRED'};return reminders},()=>{})
 await c.load(true);assert.ok(c.state.reminders)
 expired=true;await c.load(true);assert.equal(c.state.reminders,null);assert.equal(c.state.needsConsent,true)
 const before=writes;await c.load(true);assert.equal(writes,before)
})

test('ack is explicit and bound to an existing reminder version, never cancels',async()=>{
 const writes=[] as {path:string,body:unknown}[]
 const c=new RecurringRemindersController(async()=>recurring,async(path,body)=>{writes.push({path,body});return path.endsWith('/ack')?{...reminder,version:2,acknowledged:true}:reminders},()=>{})
 await c.load(true);assert.equal(writes.length,1)
 await c.ack('foreign',1);assert.equal(writes.length,1)
 await c.ack(reminder.id,1);assert.deepEqual(writes[1],{path:'/subscriptions/reminders/rem_example/ack',body:{expected_version:1}})
 assert.equal(c.state.reminders?.[0].acknowledged,true);assert.equal(c.state.reminders?.[0].version,2)
 assert.doesNotMatch(JSON.stringify(writes),/cancel|refund|confirm|verify/)
})

test('disposed requests cannot publish late sensitive data',async()=>{
 let release!: (v:unknown)=>void,entered!:()=>void,emitted=0
 const started=new Promise<void>(r=>entered=r)
 const c=new RecurringRemindersController(async()=>recurring,async()=>{entered();return new Promise(r=>release=r)},()=>emitted++)
 const work=c.load(true);await started;const before=emitted;c.dispose();release(reminders);await work;assert.equal(emitted,before)
})

test('candidate timestamps support the fractional seconds emitted by Python ISO format',()=>{
 assert.ok(parseRecurringInsights({...recurring,candidates:[{...candidate,last_seen:'2026-10-27T10:00:00.123456+08:00'}]}))
})

test('ack has a visible busy state and periodic check cannot overlap it',async()=>{
 let release!: (v:unknown)=>void,writes=0
 const c=new RecurringRemindersController(async()=>recurring,async(path)=>{writes++;return path.endsWith('/ack')?new Promise(r=>release=r):reminders},()=>{})
 await c.load(true);const work=c.ack(reminder.id,1);assert.equal(c.state.loading,true)
 await c.load(true);assert.equal(writes,2)
 release({...reminder,acknowledged:true,version:2});await work;assert.equal(c.state.loading,false)
})
