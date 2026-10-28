/** Compatibility only: never supplies bank authorization or retries business calls. */
declare const __FF_BACKEND_ID__: string
declare const __FF_FRONTEND_ID__: string
type Identity={backend_id:string;frontend_id:string}
type Meta=Identity & {schema:number;assets_id:string;build_state?:unknown}
type Fetcher=typeof fetch
export type DeploymentState='checking'|'ready'|'blocked'
const ids=['backend_id','frontend_id','assets_id'] as const
const record=(value:unknown):value is Record<string,unknown>=>!!value && typeof value==='object' && !Array.isArray(value)
function valid(value:unknown):value is Meta{
  return record(value) && value.schema===1 && ids.every(k=>typeof value[k]==='string' && /^[a-f0-9]{64}$/.test(value[k] as string))
}
export class DeploymentError extends Error{
  constructor(){super('页面与服务版本尚未匹配，请先检查本机部署；不会自动重发操作。')}
}
export class DeploymentController{
  state:DeploymentState='checking'
  private expected:Identity
  private fetcher:Fetcher
  private listeners=new Set<(state:DeploymentState)=>void>()
  private pending:Promise<boolean>|null=null
  private generation=0
  constructor(expected:Identity,fetcher:Fetcher=fetch){this.expected=expected;this.fetcher=fetcher}
  subscribe(listener:(state:DeploymentState)=>void){this.listeners.add(listener);return ()=>{this.listeners.delete(listener)}}
  private update(state:DeploymentState){this.state=state;this.listeners.forEach(fn=>fn(state))}
  block(){this.generation++;this.update('blocked')}
  check():Promise<boolean>{
    if(this.pending)return this.pending
    const generation=this.generation
    if(this.state!=='ready')this.update('checking')
    this.pending=this.inspect().then(ok=>{
      if(generation!==this.generation)return false
      this.update(ok?'ready':'blocked');return ok
    }).finally(()=>{this.pending=null})
    return this.pending
  }
  private async inspect(){
    try{
      const signal=AbortSignal.timeout(3000)
      const read=async(url:string)=>{
        const fetcher=this.fetcher
        const response=await fetcher(url,{cache:'no-store',redirect:'error',credentials:'same-origin',signal})
        if(!response.ok)throw new DeploymentError()
        return await response.json() as unknown
      }
      const meta=await read('/build-meta.json')
      if(!valid(meta) || meta.backend_id!==this.expected.backend_id || meta.frontend_id!==this.expected.frontend_id)return false
      const health=await read('/api/health')
      if(!record(health) || health.service!=='ff-agent' || health.status!=='ok')return false
      const actual=health.deployment
      return valid(actual) && record(actual) && actual.build_state==='ready' && ids.every(k=>actual[k]===meta[k])
    }catch{return false}
  }
  async requireReady(fresh=false){
    if(this.state!=='ready')throw new DeploymentError()
    if(fresh && !await this.check())throw new DeploymentError()
    if(this.state!=='ready')throw new DeploymentError()
    return this.expected.backend_id+':'+this.expected.frontend_id
  }
}
export const deployment=new DeploymentController({
  backend_id:typeof __FF_BACKEND_ID__==='string'?__FF_BACKEND_ID__:'',
  frontend_id:typeof __FF_FRONTEND_ID__==='string'?__FF_FRONTEND_ID__:''
})
