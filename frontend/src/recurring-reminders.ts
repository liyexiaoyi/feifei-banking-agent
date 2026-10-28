export type Candidate={merchant:string,amount_cents:number,occurrences:number,evidence_ids:string[],last_seen:string,rule:string,verified:false}
export type Reminder={id:string,merchant:string,amount_cents:number,charge_date:string,acknowledged:boolean,version:number,simulated:true}
export type RecurringInsights={simulated:true,as_of:string,candidates:Candidate[]}
export type ReminderList={simulated:true,reminders:Reminder[]}
export type State={candidates:Candidate[]|null,reminders:Reminder[]|null,loading:boolean,error:string,needsConsent:boolean}
type Read=(path:string)=>Promise<unknown>
type Post=(path:string,body:unknown)=>Promise<unknown>

const DATE=/^(\d{4})-(\d{2})-(\d{2})$/
const DATETIME=/^(\d{4}-\d{2}-\d{2})T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/
const REMINDER_ID=/^rem_[A-Za-z0-9_-]{1,128}$/

function isObj(v:unknown):v is Record<string,unknown>{return typeof v==='object'&&v!==null&&!Array.isArray(v)}
function text(v:unknown):v is string{return typeof v==='string'&&v.trim().length>0&&v.length<=200}
function int(v:unknown,min:number):v is number{return typeof v==='number'&&Number.isSafeInteger(v)&&v>=min}
function isDate(v:unknown):v is string{
  const m=typeof v==='string'?DATE.exec(v):null
  if(!m)return false
  const y=Number(m[1]),mo=Number(m[2]),d=Number(m[3])
  const t=new Date(Date.UTC(y,mo-1,d))
  return y>=1000&&t.getUTCFullYear()===y&&t.getUTCMonth()===mo-1&&t.getUTCDate()===d
}
function isDateTime(v:unknown):v is string{
  const m=typeof v==='string'?DATETIME.exec(v):null
  return !!m&&isDate(m[1])
}
function isConsentError(e:unknown):boolean{
  return isObj(e)&&(e.status===401||e.status===403||e.code==='SUBSCRIPTION_CONSENT_REQUIRED')
}
function statusOf(e:unknown):number{return isObj(e)&&typeof e.status==='number'?e.status:0}
function attempt(f:()=>Promise<unknown>):Promise<unknown>{
  try{return Promise.resolve(f())}catch(e){return Promise.reject(e)}
}

function parseCandidate(v:unknown):Candidate|null{
  if(!isObj(v))return null
  const {merchant,amount_cents,occurrences,evidence_ids,last_seen,rule,verified}=v
  if(!text(merchant)||!int(amount_cents,0)||!int(occurrences,2)||!text(rule)||!isDateTime(last_seen)||verified!==false)return null
  if(!Array.isArray(evidence_ids)||!evidence_ids.every(text))return null
  if(evidence_ids.length!==occurrences||new Set(evidence_ids).size!==evidence_ids.length)return null
  return {merchant,amount_cents,occurrences,evidence_ids:[...evidence_ids],last_seen,rule,verified:false}
}

function parseReminder(v:unknown):Reminder|null{
  if(!isObj(v))return null
  const {id,merchant,amount_cents,charge_date,acknowledged,version,simulated}=v
  if(typeof id!=='string'||!REMINDER_ID.test(id)||!text(merchant)||!int(amount_cents,0)||!isDate(charge_date))return null
  if(typeof acknowledged!=='boolean'||!int(version,1)||simulated!==true)return null
  return {id,merchant,amount_cents,charge_date,acknowledged,version,simulated:true}
}

export function parseRecurringInsights(v:unknown):RecurringInsights|null{
  if(!isObj(v)||v.simulated!==true||!isDate(v.as_of)||!Array.isArray(v.candidates))return null
  const candidates:Candidate[]=[]
  for(const item of v.candidates){const c=parseCandidate(item);if(!c)return null;candidates.push(c)}
  return {simulated:true,as_of:v.as_of,candidates}
}

export function parseReminders(v:unknown):ReminderList|null{
  if(!isObj(v)||v.simulated!==true||!Array.isArray(v.reminders))return null
  const reminders:Reminder[]=[]
  for(const item of v.reminders){const r=parseReminder(item);if(!r)return null;reminders.push(r)}
  if(new Set(reminders.map(r=>r.id)).size!==reminders.length)return null
  return {simulated:true,reminders}
}

export class RecurringRemindersController{
  #read:Read
  #post:Post
  #onChange:(s:State)=>void
  #state:State={candidates:null,reminders:null,loading:false,error:'',needsConsent:false}
  #gen=0
  #loading=false
  #acking=false
  #enabled=false
  #blocked=false
  #disposed=false

  constructor(read:Read,post:Post,onChange:(s:State)=>void){
    this.#read=read
    this.#post=post
    this.#onChange=onChange
  }

  get state():State{return this.#state}

  #set(patch:Partial<State>):void{
    if(this.#disposed)return
    this.#state={...this.#state,...patch}
    this.#onChange(this.#state)
  }

  async load(enabled:boolean):Promise<void>{
    if(this.#disposed||this.#loading||this.#acking||(enabled&&this.#blocked))return
    const g=++this.#gen
    this.#loading=true
    this.#enabled=enabled
    this.#set({candidates:null,reminders:null,loading:true,error:''})
    const [bill,rem]=await Promise.allSettled([
      attempt(()=>this.#read('/bill/recurring')),
      enabled?attempt(()=>this.#post('/subscriptions/reminders/check',{})):Promise.resolve(null),
    ])
    if(g!==this.#gen||this.#disposed)return
    this.#loading=false
    const insights=bill.status==='fulfilled'?parseRecurringInsights(bill.value):null
    let reminders:Reminder[]|null=null
    let error=insights?'':'周期账单数据无效或加载失败，请稍后刷新'
    let needsConsent=this.#state.needsConsent
    if(enabled){
      if(rem.status==='fulfilled'){
        const parsed=parseReminders(rem.value)
        if(parsed)reminders=parsed.reminders
        else error=error||'提醒数据无效，请稍后刷新'
      }else if(isConsentError(rem.reason)){
        this.#blocked=true
        needsConsent=true
        error='订阅提醒授权已失效，需要重新授权'
      }else error=error||'提醒检查失败，请稍后刷新'
    }
    this.#set({candidates:insights?insights.candidates:null,reminders,loading:false,error,needsConsent})
  }

  async ack(id:string,version:number):Promise<void>{
    const list=this.#state.reminders
    const old=list?.find(r=>r.id===id&&r.version===version&&!r.acknowledged)
    if(this.#disposed||!this.#enabled||this.#loading||this.#acking||this.#blocked||!list||!old)return
    const g=this.#gen
    this.#acking=true
    this.#set({loading:true,error:''})
    let res:unknown=null,err:unknown=null,failed=false
    try{res=await this.#post(`/subscriptions/reminders/${encodeURIComponent(old.id)}/ack`,{expected_version:old.version})}
    catch(e){failed=true;err=e}
    finally{this.#acking=false}
    if(g!==this.#gen||this.#disposed)return
    const next=failed?null:parseReminder(res)
    if(next&&next.id===old.id&&next.merchant===old.merchant&&next.amount_cents===old.amount_cents&&next.charge_date===old.charge_date&&next.acknowledged&&next.version===old.version+1){
      this.#set({reminders:(this.#state.reminders??[]).map(r=>r.id===old.id?next:r),loading:false,error:''})
      return
    }
    if(isConsentError(err)){
      this.#blocked=true
      this.#set({reminders:null,loading:false,needsConsent:true,error:'订阅提醒授权已失效，需要重新授权'})
      return
    }
    this.#set({reminders:null,loading:false,error:statusOf(err)===409?'提醒内容已更新，请刷新后核对':'提醒确认失败，请刷新后核对'})
  }

  dispose():void{
    this.#disposed=true
    this.#gen++
  }
}
