import {parseHandoffProgress} from './handoff-progress.ts'
import type {HandoffProgressView,HandoffStatus,HandoffTarget} from './handoff-progress.ts'
export type HandoffProgressState={view:HandoffProgressView|null;loading:boolean;busy:boolean;error:string;notice:string}
type Fetcher=(path:string,body?:unknown)=>Promise<unknown>
/** Per-case read fences and explicit writes; a failed write is never auto-replayed. */
export class HandoffProgressController{
 state:HandoffProgressState={view:null,loading:false,busy:false,error:'',notice:''}
 private sequence=0
 private disposed=false
 private path:string
 private target:HandoffTarget
 private fetcher:Fetcher
 private publish:(state:HandoffProgressState)=>void
 private key:()=>string
 constructor(target:HandoffTarget,fetcher:Fetcher,publish:(state:HandoffProgressState)=>void,key:()=>string=()=>crypto.randomUUID()){
  this.target=target;this.fetcher=fetcher;this.publish=publish;this.key=key
  this.path='/plans/'+encodeURIComponent(target.plan_id)+'/handoff/progress'
 }
 private set(change:Partial<HandoffProgressState>){if(!this.disposed){this.state={...this.state,...change};this.publish(this.state)}}
 dispose(){this.disposed=true;this.sequence++}
 private async read(){
  const sequence=++this.sequence
  this.set({view:null,loading:true,error:''})
  try{
   const view=parseHandoffProgress(await this.fetcher(this.path),this.target)
   if(!view)throw Error('invalid progress')
   if(sequence===this.sequence)this.set({view})
  }catch{if(sequence===this.sequence)this.set({view:null,error:'暂时无法核对本地进度，请刷新后再操作。'})}
  finally{if(sequence===this.sequence)this.set({loading:false})}
 }
 async refresh(){if(!this.disposed&&!this.state.busy){this.set({notice:''});await this.read()}}
 async update(status:Exclude<HandoffStatus,'OPEN'>){
  const view=this.state.view
  if(this.disposed||!view||this.state.busy||this.state.loading||this.state.error||view.status==='CLOSED'||view.status===status)return
  this.sequence++;this.set({busy:true,error:'',notice:''})
  try{
   const raw=await this.fetcher(this.path,{expected_version:view.version,status,request_key:this.key()})
   const next=parseHandoffProgress(raw,this.target)
   if(!next||!next.receipt||next.receipt.from_version!==view.version||next.receipt.to_status!==status)throw Error('invalid update receipt')
   this.set({view:next,notice:'已更新本地记录。交易状态仍需单独核对。'})
  }catch(error){
   await this.read()
   const conflict=!!error&&typeof error==='object'&&'status' in error&&error.status===409
   this.set({notice:conflict?'记录已被更新，请核对刷新后的进度，再决定是否操作；不会自动重试。':'更新结果暂时没有确认，已重新核对当前记录；不会自动重试。'})
  }finally{this.set({busy:false})}
 }
}
